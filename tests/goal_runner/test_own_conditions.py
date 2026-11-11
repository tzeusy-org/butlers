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
from butlers.core.goal_wake import names_input
from butlers.goal_runner import tools as goal_tools
from butlers.goal_runner.own_conditions import (
    OwnEffect,
    OwnReply,
    events_for,
    grant_for,
    named,
    own_predicate,
    snapshot_for,
)

NOW = datetime(2026, 11, 11, 9, tzinfo=UTC)
PARKED, SENT, PROMPT = str(uuid4()), str(uuid4()), str(uuid4())


def decision(subject: str = PARKED, outcome: str = "approved") -> dict:
    return {"tag": "decision", "subject_id": subject, "outcome": outcome}


def settled(effect: str = SENT, *statuses: str) -> dict:
    return {
        "tag": "effect_settled",
        "effect_id": effect,
        "status_set": list(statuses or ["succeeded"]),
    }


def owner_input(goal: str, binding: str) -> dict:
    return {"tag": "owner_input", "goal_id": goal, "binding_ref": binding}


@pytest.mark.parametrize(
    "leaf",
    [
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
def test_clock_and_perception_leaves_are_not_goal_own(leaf):
    validate({"tag": "any", "children": [decision(), leaf]})  # valid S4, just not goal-own
    with pytest.raises(PredicateError, match="goal-own"):
        own_predicate({"tag": "any", "children": [decision(), leaf]})


def test_a_goal_own_predicate_is_returned_canonical():
    goal = str(uuid4())
    predicate = {
        "tag": "any",
        "children": [
            decision(),
            settled(SENT, "failed", "succeeded"),
            owner_input(goal, PROMPT),
        ],
    }
    assert own_predicate(predicate) == predicate
    refs = named(predicate)
    assert (refs.decisions, refs.effects) == (frozenset({PARKED}), frozenset({SENT}))
    assert (refs.inputs, refs.goals) == (frozenset({PROMPT}), frozenset({goal}))


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


def test_only_a_live_owner_prompt_is_a_binding_to_wait_on():
    goal = str(uuid4())
    effects = [
        OwnEffect(PARKED, "parked", True),
        OwnEffect(SENT, "committed", False),
        OwnEffect(PROMPT, "committed", False, prompt=True),
    ]
    grant = grant_for(goal, owner_input(goal, PROMPT), effects)
    assert grant.sources == frozenset({"goal.inbox"})
    assert grant.bindings == frozenset({PROMPT})


def test_an_unread_reply_is_owner_input_at_registration():
    snapshot = snapshot_for(NOW, [OwnEffect(PROMPT, "committed", False, prompt=True)], [PROMPT])
    assert snapshot.inputs == frozenset({PROMPT})


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


def test_an_unread_reply_is_an_owner_input_event_on_its_prompt():
    goal, inbox = str(uuid4()), str(uuid4())
    (event,) = events_for([], [OwnReply(inbox, goal, PROMPT, CHANGED)])
    assert (event.source, event.tag) == ("goal.inbox", "owner_input")
    assert dict(event.data) == {"goal_id": goal, "binding_ref": PROMPT}
    assert event.at == CHANGED and event.position == int(CHANGED.timestamp() * 1_000_000)
    # One event per inbox row: a redelivery is the same event, another reply is not.
    (again,) = events_for([], [OwnReply(inbox, goal, PROMPT, CHANGED)])
    (other,) = events_for([], [OwnReply(str(uuid4()), goal, PROMPT, CHANGED)])
    assert again.id == event.id != other.id


async def test_a_reply_to_the_named_prompt_fires_an_owner_input_wait():
    goal = str(uuid4())
    elsewhere = str(uuid4())
    predicate = owner_input(goal, PROMPT)
    effects = [
        OwnEffect(PROMPT, "committed", False, prompt=True),
        OwnEffect(elsewhere, "committed", False, prompt=True),
    ]
    evaluator = ConditionEvaluator(InMemoryConditionStore())
    registered = await evaluator.register(
        instance_id="i0",
        predicate=predicate,
        grant=grant_for(goal, predicate, effects),
        goal_id=goal,
        label=Label.owner(),
        snapshot=snapshot_for(NOW, effects),
        expires_at=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert registered.intent is None

    other = [OwnReply(str(uuid4()), goal, elsewhere, CHANGED)]
    assert (await evaluator.evaluate("i0", events_for([], other), now=CHANGED)).intent is None
    reply = [*other, OwnReply(str(uuid4()), goal, PROMPT, CHANGED)]
    fired = await evaluator.evaluate("i0", events_for([], reply), now=CHANGED)
    assert fired.intent is not None


def test_a_reply_joins_a_conditions_wait_only_if_its_predicate_names_the_prompt():
    goal = str(uuid4())
    names = {"tag": "all", "children": [decision(), owner_input(goal, PROMPT)]}
    assert names_input(names, PROMPT)
    assert not names_input(names, str(uuid4()))
    assert not names_input(decision(), PROMPT)
    # A stored predicate that is not well formed names nothing; it never makes a reply join.
    for malformed in ({"tag": "owner_input"}, {"tag": "all"}, {}, None, "owner_input"):
        assert not names_input(malformed, PROMPT)


async def test_an_unread_reply_at_registration_fires_at_once():
    goal = str(uuid4())
    predicate = owner_input(goal, PROMPT)
    effects = [OwnEffect(PROMPT, "committed", False, prompt=True)]
    evaluator = ConditionEvaluator(InMemoryConditionStore())
    registered = await evaluator.register(
        instance_id="i0",
        predicate=predicate,
        grant=grant_for(goal, predicate, effects),
        goal_id=goal,
        label=Label.owner(),
        snapshot=snapshot_for(NOW, effects, [PROMPT]),
        expires_at=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert registered.intent is not None


# An owner edit records the stored call ``rejected`` and dispatches the edit as a new effect
# under the same approval: the owner neither approved nor refused that call as asked.
EDITED = OwnEffect(PARKED, "rejected", True, edited=True)


def test_an_edited_approval_is_its_own_decision_and_settles_cancelled():
    snapshot = snapshot_for(NOW, [EDITED])
    assert snapshot.decisions == {PARKED: "edited"}
    assert snapshot.effects == {PARKED: "cancelled"}
    events = {e.tag: dict(e.data) for e in events_for([(EDITED, CHANGED)])}
    assert events == {
        "decision": {"subject_id": PARKED, "outcome": "edited"},
        "effect_settled": {"effect_id": PARKED, "status": "cancelled"},
    }


async def evaluated(predicate: dict, effects: list[OwnEffect], fed: list[OwnEffect]):
    """Register ``predicate`` over ``effects``, then feed ``fed``: (registered, evaluated)."""
    goal = str(uuid4())
    evaluator = ConditionEvaluator(InMemoryConditionStore())
    registered = await evaluator.register(
        instance_id="i0",
        predicate=predicate,
        grant=grant_for(goal, predicate, effects),
        goal_id=goal,
        label=Label.owner(),
        snapshot=snapshot_for(NOW, effects),
        expires_at=datetime(2026, 11, 12, tzinfo=UTC),
    )
    fed_events = events_for([(effect, CHANGED) for effect in fed])
    return registered, await evaluator.evaluate("i0", fed_events, now=CHANGED)


async def test_an_edited_approval_fires_neither_an_approved_nor_a_rejected_wait():
    either = {"tag": "any", "children": [decision(PARKED), decision(PARKED, "rejected")]}
    pending = [OwnEffect(PARKED, "parked", True)]
    registered, fed = await evaluated(either, pending, [EDITED])
    assert registered.intent is None and fed.intent is None
    # Already edited when the lead waits: still neither.
    registered, _ = await evaluated(either, [EDITED], [])
    assert registered.intent is None


async def test_an_edited_approval_still_settles_its_effect():
    pending = [OwnEffect(PARKED, "parked", True)]
    _, fed = await evaluated(settled(PARKED, "cancelled"), pending, [EDITED])
    assert fed.intent is not None


_STATUSES = ("requested", "parked", "approved", "dispatched", "committed", "failed", "unknown")
_STATUSES += ("rejected", "expired", "cancelled", "refused")
_REARM_LEAVES = [decision(PARKED, o) for o in ("approved", "rejected", "expired", "cancelled")] + [
    settled(PARKED, s) for s in ("succeeded", "failed", "unknown", "cancelled")
]


@pytest.mark.parametrize("leaf", _REARM_LEAVES)
@pytest.mark.parametrize(
    "effect",
    [OwnEffect(PARKED, status, True) for status in _STATUSES] + [EDITED],
    ids=lambda e: f"{e.status}{'-edited' if e.edited else ''}",
)
async def test_old_facts_fed_again_never_wake_a_rearmed_generation(leaf, effect):
    """A rearmed generation registers on the current snapshot with no position floor. The
    feed then re-sends the same rows (at a later time): that must change no leaf, so only a
    new fact can wake it. A snapshot that already holds is refused before registering."""
    registered, fed = await evaluated(leaf, [effect], [effect])
    if registered.intent is None:
        assert fed.intent is None
        assert fed.instance.leaves == registered.instance.leaves
