"""Perception timeline rows as transition events carry the row's label (bu-dhloi1.305 PR5c).

Pure: rows shaped as ``public.perception_timeline`` returns them, fed to the evaluator through
its production API. Nothing reads the database or feeds a live instance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from butlers.conditions import ConditionEvaluator, Grant, Snapshot
from butlers.conditions.memory import InMemoryConditionStore
from butlers.conditions.transitions import source_for, timeline_label, transition_event
from butlers.core.goal_labels import CATEGORIES, Label

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
GOAL = str(uuid5(NAMESPACE_URL, "goal"))
PLACE = str(uuid5(NAMESPACE_URL, "place"))
PRINCIPAL = str(uuid5(NAMESPACE_URL, "principal"))
SOURCE = "perception:owner.place"
LEAF = {
    "tag": "transition",
    "kind": "owner.place",
    "subject": PLACE,
    "from": "known",
    "to": "unknown",
}
#: As perception_write stores it: resolved refs plus world, and all six categories.
STORED = {
    "version": 1,
    "label_instr": [PRINCIPAL, "world"],
    "label_data": [PRINCIPAL, "world"],
    "label_sens": sorted(CATEGORIES),
}


def row(**overrides) -> dict:
    """A timeline row as the timeline function returns it (JSON: strings for times)."""
    base = {
        "id": str(uuid4()),
        "source": "home-assistant",
        "event_key": str(uuid4()),
        "kind": "owner.place",
        "subject_entity": PLACE,
        "class": "transition",
        "operation": "assert",
        "retracts_event_id": None,
        "at": NOW.isoformat(),
        "confirmed_at": NOW.isoformat(),
        "late": False,
        "from_state": "known",
        "to_state": "unknown",
        "labels": STORED,
        "created_at": (NOW + timedelta(seconds=1)).isoformat(),
        "retracted": False,
        "retraction_event_id": None,
    }
    return {**base, **overrides}


async def fired(event):
    evaluator = ConditionEvaluator(InMemoryConditionStore())
    await evaluator.register(
        instance_id="t0",
        predicate=LEAF,
        grant=Grant(goal_id=GOAL, sources=frozenset({SOURCE}), subjects=frozenset({PLACE})),
        goal_id=GOAL,
        label=Label.owner(),
        snapshot=Snapshot(now=NOW),
        expires_at=NOW + timedelta(hours=1),
    )
    return (await evaluator.evaluate("t0", [event], now=NOW)).intent


def test_a_transition_row_is_the_leafs_event():
    source = row()
    event = transition_event(source)
    assert event is not None
    assert (event.id, event.source, event.tag) == (source["id"], SOURCE, "transition")
    assert dict(event.data) == {
        "kind": "owner.place",
        "subject": PLACE,
        "from": "known",
        "to": "unknown",
    }
    assert event.at == NOW
    assert event.position == int((NOW + timedelta(seconds=1)).timestamp() * 1_000_000)
    assert source_for("owner.place") == SOURCE
    # A redelivered row is the same event.
    assert transition_event(source) == event


def test_rows_read_straight_from_the_table_map_the_same():
    """A driver row carries datetimes and a UUID subject, and jsonb may arrive as text."""
    source = row()
    raw = {
        **source,
        "at": NOW,
        "created_at": NOW + timedelta(seconds=1),
        "subject_entity": uuid5(NAMESPACE_URL, "place"),
        "labels": json.dumps(STORED),
    }
    assert transition_event(raw) == transition_event(source)


@pytest.mark.parametrize("kind", ["record", "occurrence", "point"])
def test_a_row_that_is_no_transition_is_no_event(kind):
    assert transition_event(row(**{"class": kind})) is None


def test_the_rows_principals_become_external_principals_and_world_stays_world():
    label = timeline_label(STORED)
    assert label == Label(
        frozenset({f"external:{PRINCIPAL}", "world"}),
        frozenset({f"external:{PRINCIPAL}", "world"}),
        CATEGORIES,
    )
    assert label.exposed


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "not json",
        {},
        {**STORED, "version": 2},
        {**STORED, "label_instr": []},
        {**STORED, "label_instr": ["owner"]},  # perception has no owner principal to claim
        {**STORED, "label_data": ["not-a-uuid"]},
        {**STORED, "label_sens": ["gossip"]},
        {**STORED, "extra": []},
    ],
)
def test_a_missing_or_malformed_label_is_missing_provenance(stored):
    assert timeline_label(stored) is None
    assert transition_event(row(labels=stored)).label is None


async def test_a_fire_on_a_labelled_row_joins_the_rows_label():
    intent = await fired(transition_event(row()))
    assert intent is not None
    assert intent.label.covers(Label.owner()) and intent.label.covers(timeline_label(STORED))


async def test_a_fire_on_an_unlabelled_row_joins_unknown_not_owner():
    intent = await fired(transition_event(row(labels=None)))
    assert intent is not None
    assert intent.label != Label.owner()
    assert intent.label.covers(Label.unknown()) and intent.label.exposed


@pytest.mark.parametrize(
    "flags",
    [{"late": True}, {"retracted": True}, {"operation": "retract"}],
    ids=["late", "retracted", "retraction"],
)
async def test_late_retracted_and_retraction_rows_never_fire(flags):
    event = transition_event(row(**flags))
    assert event is not None
    assert await fired(event) is None
