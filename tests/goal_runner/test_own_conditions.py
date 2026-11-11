"""``goal.wait_until`` reads only the goal's own ledger (RFC 0038 S2.2, bu-dhloi1.305 PR1).

Pure checks: which leaves are goal-own, the grant the server derives from the goal's effects,
and the registration snapshot each effect status contributes. The database path is in
``test_goal_wait_until``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from butlers.capability_broker.contracts import BrokerRefused, Reason
from butlers.conditions import ConditionEvaluator, PredicateError, validate
from butlers.conditions.memory import InMemoryConditionStore
from butlers.conditions.service import StoreConflict
from butlers.conditions.sql import ConnectionConditionStore
from butlers.core.goal_labels import Label
from butlers.goal_runner import tools as goal_tools
from butlers.goal_runner.own_conditions import (
    OwnEffect,
    events_for,
    grant_for,
    named,
    own_predicate,
    snapshot_for,
)

NOW = datetime(2026, 11, 11, 9, tzinfo=UTC)
PARKED, SENT = str(uuid4()), str(uuid4())


def decision(subject: str = PARKED, outcome: str = "approved") -> dict:
    return {"tag": "decision", "subject_id": subject, "outcome": outcome}


def settled(effect: str = SENT, *statuses: str) -> dict:
    return {
        "tag": "effect_settled",
        "effect_id": effect,
        "status_set": list(statuses or ["succeeded"]),
    }


@pytest.mark.parametrize(
    "leaf",
    [
        {"tag": "owner_input", "goal_id": str(uuid4()), "binding_ref": str(uuid4())},
        {"tag": "at", "instant": "2026-11-12T09:00:00Z", "zone": "UTC"},
        {
            "tag": "transition",
            "kind": "owner.place",
            "subject": None,
            "from": "unknown",
            "to": "known",
        },
    ],
)
def test_only_decision_and_effect_leaves_are_goal_own(leaf):
    validate({"tag": "any", "children": [decision(), leaf]})  # valid S4, just not goal-own
    with pytest.raises(PredicateError, match="goal-own"):
        own_predicate({"tag": "any", "children": [decision(), leaf]})


def test_a_goal_own_predicate_is_returned_canonical():
    predicate = {"tag": "all", "children": [decision(), settled(SENT, "failed", "succeeded")]}
    assert own_predicate(predicate) == predicate
    assert named(predicate) == (frozenset({PARKED}), frozenset({SENT}))


def test_free_text_is_not_a_predicate():
    with pytest.raises(PredicateError):
        own_predicate("when the owner says yes")


def test_the_grant_is_derived_from_the_goals_own_effects():
    goal = str(uuid4())
    effects = [OwnEffect(PARKED, "parked", True), OwnEffect(SENT, "committed", False)]
    grant = grant_for(goal, decision(), effects)
    assert grant.goal_id == goal
    assert grant.sources == frozenset({"goal.decision"})
    # Only an effect that went through approval has a decision to wait on.
    assert grant.decisions == frozenset({PARKED})
    assert grant.effects == frozenset({PARKED, SENT})
    assert grant.bindings == frozenset() and grant.subjects == frozenset()


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("parked", None),
        ("approved", "approved"),
        ("dispatched", "approved"),
        ("committed", "approved"),
        ("failed", "approved"),
        ("unknown", "approved"),
        ("rejected", "rejected"),
        ("expired", "expired"),
        ("cancelled", "cancelled"),
        ("refused", None),
    ],
)
def test_a_decision_is_read_from_the_approval_effect_status(status, outcome):
    snapshot = snapshot_for(NOW, [OwnEffect(PARKED, status, True)])
    assert snapshot.decisions.get(PARKED) == outcome


def test_an_effect_without_approval_has_no_decision():
    snapshot = snapshot_for(NOW, [OwnEffect(SENT, "committed", False)])
    assert snapshot.decisions == {}


@pytest.mark.parametrize(
    ("status", "settled_as"),
    [
        ("requested", None),
        ("parked", None),
        ("approved", None),
        ("dispatched", None),
        ("committed", "succeeded"),
        ("failed", "failed"),
        ("unknown", "unknown"),
        ("cancelled", "cancelled"),
        ("rejected", "cancelled"),
        ("expired", "cancelled"),
        ("refused", "cancelled"),
    ],
)
def test_an_effect_settles_only_once_it_can_no_longer_change(status, settled_as):
    snapshot = snapshot_for(NOW, [OwnEffect(SENT, status, False)])
    assert snapshot.effects.get(SENT) == settled_as
    assert snapshot.now == NOW and snapshot.inputs == frozenset()


class _Connection:
    def __init__(self, error: Exception) -> None:
        self.error = error

    @asynccontextmanager
    async def transaction(self):
        raise self.error
        yield  # pragma: no cover


@pytest.mark.parametrize(
    ("error", "mapped"),
    [
        (asyncpg.SerializationError("x"), StoreConflict),
        (asyncpg.DeadlockDetectedError("x"), StoreConflict),
        (asyncpg.InterfaceError("x"), ConnectionError),
        (TimeoutError(), ConnectionError),
    ],
)
async def test_the_connection_store_maps_driver_errors_like_the_pool_store(error, mapped):
    store = ConnectionConditionStore(_Connection(error))  # type: ignore[arg-type]
    with pytest.raises(mapped):
        async with store.transaction():
            pass


async def test_wait_until_is_dark_to_leads_until_the_event_feed_lands():
    # Flipped when the goal-event feed is wired live (after PR5), together with this test.
    assert goal_tools.WAIT_UNTIL_LIVE is False
    runner = MagicMock()
    runner.wait_until = AsyncMock(return_value="waiting")
    tools = goal_tools.GoalTools(MagicMock(), runner)
    assert "goal__wait_until" not in [t.name for t in tools.listed(0)]
    arguments = {"predicate": {}, "deadline": "2099-01-01T00:00:00+00:00", "max_wakes": 1}
    with pytest.raises(BrokerRefused) as refused:
        await tools.call(uuid4(), "token", "goal__wait_until", arguments)
    assert refused.value.reason is Reason.CAPABILITY
    runner.wait_until.assert_not_awaited()


CHANGED = datetime(2026, 11, 11, 10, tzinfo=UTC)


def test_a_decision_event_names_the_effect_as_its_subject():
    events = events_for([(OwnEffect(PARKED, "rejected", True), CHANGED)])
    (event,) = [e for e in events if e.tag == "decision"]
    assert event.source == "goal.decision"
    assert dict(event.data) == {"subject_id": PARKED, "outcome": "rejected"}
    assert event.at == CHANGED and event.position == int(CHANGED.timestamp() * 1_000_000)


def test_an_approved_effect_that_ran_is_both_a_decision_and_a_settlement():
    events = events_for([(OwnEffect(PARKED, "committed", True), CHANGED)])
    assert {(e.tag, tuple(sorted(e.data.values()))) for e in events} == {
        ("decision", tuple(sorted((PARKED, "approved")))),
        ("effect_settled", tuple(sorted((PARKED, "succeeded")))),
    }


@pytest.mark.parametrize("status", ["requested", "parked"])
def test_an_undecided_effect_feeds_nothing(status):
    assert events_for([(OwnEffect(PARKED, status, True), CHANGED)]) == []


def test_a_dispatched_approval_is_decided_but_not_yet_settled():
    (event,) = events_for([(OwnEffect(PARKED, "dispatched", True), CHANGED)])
    assert event.tag == "decision" and event.data["outcome"] == "approved"


def test_a_redelivered_event_is_the_same_event():
    first = events_for([(OwnEffect(SENT, "failed", False), CHANGED)])
    again = events_for([(OwnEffect(SENT, "failed", False), CHANGED)])
    assert [e.id for e in first] == [e.id for e in again]
    other = events_for([(OwnEffect(SENT, "unknown", False), CHANGED)])
    assert first[0].id != other[0].id


async def test_fed_events_fire_a_registered_goal_own_wait_once():
    goal = str(uuid4())
    predicate = {"tag": "all", "children": [decision(PARKED), settled(SENT, "succeeded")]}
    pending = [OwnEffect(PARKED, "parked", True), OwnEffect(SENT, "dispatched", False)]
    evaluator = ConditionEvaluator(InMemoryConditionStore())
    registered = await evaluator.register(
        instance_id="i0",
        predicate=predicate,
        grant=grant_for(goal, predicate, pending),
        goal_id=goal,
        label=Label.owner(),
        snapshot=snapshot_for(NOW, pending),
        expires_at=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert registered.intent is None

    half = [(OwnEffect(PARKED, "approved", True), CHANGED), (pending[1], CHANGED)]
    assert (await evaluator.evaluate("i0", events_for(half), now=CHANGED)).intent is None
    done = [
        (OwnEffect(PARKED, "approved", True), CHANGED),
        (OwnEffect(SENT, "committed", False), CHANGED),
    ]
    fired = await evaluator.evaluate("i0", events_for(done), now=CHANGED)
    assert fired.intent is not None
    replay = await evaluator.evaluate("i0", events_for(done), now=CHANGED)
    assert replay.intent == fired.intent
