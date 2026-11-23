"""Source adapter for the presence reducer: typed, coordinate-free HA observations only."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from butlers.jobs.home_presence_reducer import _request, ha_observations, home_place
from butlers.owner_places import OwnerPlace
from butlers.perception.presence import OBSERVATION_FRESH, Decision

HOME = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
OWNER_ENTITIES = frozenset({"person.owner"})


def row(entity, state, age=timedelta(minutes=1)):
    return {"entity_id": entity, "state": state, "last_updated": NOW - age}


def observe(rows):
    return ha_observations(rows, owner_entity_ids=OWNER_ENTITIES, place_ref=HOME, now=NOW)


def test_only_fresh_owner_home_and_not_home_states_qualify():
    rows = [
        row("person.owner", "home"),
        row("person.housemate", "not_home"),
        row("person.owner", "work"),
        row("person.owner", "unavailable"),
        row("person.owner", "not_home", OBSERVATION_FRESH + timedelta(seconds=1)),
    ]
    result = observe(rows)
    assert [(o.side, o.place_ref) for o in result] == [("inside", HOME)]
    assert not result[0].ssid_match


def test_not_home_is_an_outside_observation_with_a_stable_opaque_key():
    (first,) = observe([row("person.owner", "not_home")])
    (second,) = observe([row("person.owner", "not_home")])
    assert first.side == "outside" and first.key == second.key and "person" not in first.key


def test_home_place_requires_exactly_one_home():
    def place(kind, n):
        return OwnerPlace(UUID(int=n), "x", kind, 0.0, 0.0, 100)

    assert home_place((place("home", 1), place("work", 2))) == UUID(int=1)
    assert home_place((place("home", 1), place("home", 2))) is None
    assert home_place((place("work", 2),)) is None


def test_published_request_carries_a_closed_coordinate_free_value():
    decision = Decision(
        "leave", "outside", HOME, NOW, NOW, NOW + timedelta(minutes=3), ("owntracks",)
    )
    body = _request(decision, subject=UUID(int=9), generation=4).payload()
    assert body["typed_value_or_ref"] == {
        "presence": "outside",
        "place_ref": str(HOME),
        "locality_ref": None,
    }
    assert body["evidence_refs"][0]["source"] == "owntracks"
    flat = str(body).lower()
    assert "lat" not in flat.replace("platform", "") and "lon" not in flat and "ssid" not in flat
