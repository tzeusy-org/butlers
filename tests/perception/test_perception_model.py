"""REQ-perception-registry-001/002 closed request and immutable identity owners."""

import copy
from datetime import timedelta
from uuid import uuid5

import pytest

from butlers.perception.model import (
    ACTIVITIES,
    ALIASES,
    KINDS,
    MODES,
    NAMESPACE,
    EventRequest,
    canonical,
    event_id,
)
from tests.perception.factories import AT, request, wire_time

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_closed_registry_round_trip(kind):
    body = request(kind=kind)
    parsed = EventRequest(body)
    assert parsed.payload() == body
    # A caller-owned mutable body, including nested labels, never changes the frozen request.
    body["labels"]["label_sens"].append("health")
    assert parsed.payload()["labels"]["label_sens"] == ["location"]
    assert event_id(parsed.payload()["source"], "first") == uuid5(
        NAMESPACE, canonical(["perception-event-v1", parsed.payload()["source"], "first"])
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(writer="perception_owner"),
        lambda b: b.update(late=False),
        lambda b: b.update(version=True),
        lambda b: b.update(expected_generation=True),
        lambda b: b.update(expected_generation=2**63 - 1),
        lambda b: b.update(event_key="x" * 257),
        lambda b: b.update(kind="owner.presence"),
        lambda b: b["typed_value_or_ref"].update(latitude=1),
        lambda b: b["typed_value_or_ref"].update(title="untrusted prose"),
        lambda b: b["typed_value_or_ref"].update(place_ref="named home"),
        lambda b: b.update(confirmed_at=wire_time(AT - timedelta(seconds=1))),
        lambda b: b.update(at="infinity"),
        lambda b: b["labels"].update(label_sens=["unknown_category"]),
        lambda b: b["labels"].update(label_instr=["owner"]),
        lambda b: b.update(evidence_refs=[]),
    ],
    ids=[
        "writer-forgery",
        "late-forgery",
        "bool-version",
        "bool-generation",
        "overflow",
        "key-bound",
        "alias-not-writer",
        "coordinates",
        "prose",
        "nonopaque-reference",
        "negative-confirmation",
        "infinite-time",
        "unknown-category",
        "payload-identity",
        "missing-evidence",
    ],
)
def test_untyped_or_forged_request_refuses(mutation):
    body = request()
    mutation(body)
    with pytest.raises((ValueError, TypeError)):
        EventRequest(body)


def test_aliases_preserve_read_identity_without_granting_modes():
    assert set(ACTIVITIES) <= ALIASES.keys()
    assert {k: ALIASES[k] for k in ("at_home", "in_space", "away", "dnd")} == {
        "at_home": "owner.place",
        "in_space": "owner.place",
        "away": "owner.place",
        "dnd": "owner.suppression",
    }
    assert len(MODES) == 8 and "in_flight" not in MODES
    for kind, value in [
        ("owner.mode", ["focused"]),
        ("owner.activity", ["focus"]),
        ("owner.mode", ["in_flight"]),
        ("owner.activity", ["sick", "sick"]),
    ]:
        with pytest.raises(ValueError):
            EventRequest(request(kind=kind, value=value))
    original = request()
    copied = copy.deepcopy(original)
    assert EventRequest(copied).payload() == original
    assert event_id("home.dots", "é") != event_id("home.dots", "e")
