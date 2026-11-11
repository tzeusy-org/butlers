"""``goal.wait_until`` reads only the goal's own ledger (RFC 0038 S2.2, bu-dhloi1.305 PR1).

Pure checks: which leaves are goal-own, the grant the server derives from the goal's effects,
and the registration snapshot each effect status contributes. The database path is in
``test_goal_wait_until``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from butlers.conditions import PredicateError, validate
from butlers.conditions.service import StoreConflict
from butlers.conditions.sql import ConnectionConditionStore
from butlers.goal_runner.own_conditions import (
    OwnEffect,
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
