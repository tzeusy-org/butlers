"""Critical-entity classification (bu-jv1moa.122, REQ-module-home-assistant-002).

The pure gate: which calls must be treated as naming an entity the owner marked critical.
Module and broker behavior over this gate is in ``test_module_home_assistant.py`` and
``tests/capability_broker/test_actuation_risk.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.roster_imports import roster_module

pytestmark = pytest.mark.unit

actuation = roster_module("home", "modules/actuation")
names_critical_entity = actuation.names_critical_entity

CRITICAL = frozenset({"switch.freezer"})
CACHE = {
    "light.kitchen": {},
    "switch.freezer": {},
    "switch.lamp": {},
    "switch.group_ok": {"entity_id": ["switch.lamp", "light.kitchen"]},
    "switch.group_bad": {"entity_id": ["switch.lamp", "switch.freezer"]},
    "switch.nested_bad": {"entity_id": ["switch.group_ok", "switch.group_bad"]},
    "switch.empty": {"entity_id": []},
    "switch.malformed": {"entity_id": ["switch.lamp", 7]},
    "switch.string_members": {"entity_id": "switch.lamp"},
    "switch.loop_a": {"entity_id": ["switch.loop_b"]},
    "switch.loop_b": {"entity_id": ["switch.loop_a"]},
}


def _attributes(entity_id):
    return CACHE.get(entity_id)


def _critical(domain, service, target=None, data=None, *, registry=CRITICAL, attributes=None):
    return names_critical_entity(
        domain,
        service,
        target,
        data,
        registry=registry,
        attributes=attributes or _attributes,
    )


def test_an_empty_registry_changes_nothing() -> None:
    for call in (
        ("light", "turn_on", {"entity_id": "light.kitchen"}),
        ("light", "turn_on", {"area_id": "kitchen"}),
        ("scene", "turn_on", {"entity_id": "scene.movie"}),
        ("button", "press", {"entity_id": "button.x"}),
    ):
        assert not _critical(*call, registry=frozenset())


def test_a_call_naming_a_critical_entity_is_critical_and_others_are_not() -> None:
    assert _critical("switch", "turn_off", {"entity_id": "switch.freezer"})
    assert _critical("switch", "turn_off", {"entity_id": ["switch.lamp", "switch.freezer"]})
    assert not _critical("light", "turn_on", {"entity_id": "light.kitchen"})
    assert not _critical("switch", "turn_off", {"entity_id": "switch.lamp"})


@pytest.mark.parametrize(
    "target",
    [
        None,
        {"area_id": "kitchen"},
        {"device_id": "abc"},
        {"label_id": "heating"},
        {"entity_id": "all"},
        {"entity_id": "light.kitchen", "area_id": "kitchen"},
        {"entity_id": []},
        {"entity_id": "light.kitchen, light.other"},
    ],
)
def test_a_target_that_is_not_an_explicit_entity_list_is_critical(target) -> None:
    assert _critical("light", "turn_off", target)


def test_a_data_carried_selector_is_critical() -> None:
    assert _critical("light", "turn_off", {"entity_id": "light.kitchen"}, {"area_id": "x"})


@pytest.mark.parametrize(
    ("domain", "service"),
    [
        ("scene", "turn_on"),
        ("script", "turn_on"),
        ("script", "morning_routine"),
        ("automation", "trigger"),
        ("homeassistant", "turn_on"),
        ("homeassistant", "turn_off"),
        ("homeassistant", "toggle"),
        ("homeassistant", "restart"),
        ("button", "press"),
        ("input_button", "press"),
        ("automation", "turn_on"),
        ("vacuum", "start"),
    ],
)
def test_indirect_and_unlisted_actuators_are_critical_on_their_own_entity(domain, service) -> None:
    assert actuation.is_indirect_actuator(domain, service)
    assert _critical(domain, service, {"entity_id": f"{domain}.fixture"})


@pytest.mark.parametrize("registry", [CRITICAL, frozenset(), None])
def test_a_safe_read_is_never_promoted(registry) -> None:
    assert not actuation.is_indirect_actuator("homeassistant", "update_entity")
    assert not _critical("homeassistant", "update_entity", None, registry=registry)


@pytest.mark.parametrize(
    "call",
    [
        ("light", "turn_on", {"entity_id": "light.kitchen"}),
        ("lock", "unlock", {"entity_id": "lock.door"}),
        ("scene", "turn_on", None),
    ],
)
def test_an_unreadable_registry_makes_every_call_critical(call) -> None:
    assert _critical(*call, registry=None)


def test_a_group_with_a_critical_member_is_critical_through_nesting() -> None:
    assert _critical("switch", "turn_off", {"entity_id": "switch.group_bad"})
    assert _critical("switch", "turn_off", {"entity_id": "switch.nested_bad"})
    assert not _critical("switch", "turn_off", {"entity_id": "switch.group_ok"})


@pytest.mark.parametrize(
    "entity_id",
    [
        "switch.unseen",
        "switch.empty",
        "switch.malformed",
        "switch.string_members",
        "switch.loop_a",
    ],
)
def test_unreadable_membership_fails_closed(entity_id) -> None:
    assert _critical("switch", "turn_off", {"entity_id": entity_id})


def test_nesting_deeper_than_eight_fails_closed_and_eight_is_allowed() -> None:
    chain = {f"switch.g{i}": {"entity_id": [f"switch.g{i + 1}"]} for i in range(8)}
    chain["switch.g8"] = {"entity_id": ["switch.lamp"]}
    chain["switch.lamp"] = {}
    # g0 -> ... -> g8 -> lamp: the lamp sits at depth 9.
    assert _critical("switch", "turn_off", {"entity_id": "switch.g0"}, attributes=chain.get)
    # g1 -> ... -> g8 -> lamp: the lamp sits at depth 8, which is still readable.
    assert not _critical("switch", "turn_off", {"entity_id": "switch.g1"}, attributes=chain.get)


def test_a_helper_wrapping_an_unlisted_entity_looks_ordinary_until_the_owner_marks_it() -> None:
    cache = {"light.wrapper": {}, "switch.freezer": {}}
    assert not _critical("light", "turn_off", {"entity_id": "light.wrapper"}, attributes=cache.get)
    assert _critical(
        "light",
        "turn_off",
        {"entity_id": "light.wrapper"},
        registry=frozenset({"light.wrapper"}),
        attributes=cache.get,
    )


def test_promotion_is_at_least_consequential_and_never_lowers_or_touches_a_safe_read() -> None:
    reversible = actuation.effective_actuation_risk(
        "light", "turn_on", {"entity_id": "light.kitchen"}, None
    )
    assert reversible.risk is actuation.ActuationRisk.REVERSIBLE
    promoted = reversible.as_critical()
    assert promoted.risk is actuation.ActuationRisk.CONSEQUENTIAL
    assert promoted.critical
    assert promoted.approval_reason == "reversible call promoted: critical_entity"

    protected = actuation.effective_actuation_risk(
        "lock", "unlock", {"entity_id": "lock.door"}, None
    ).as_critical()
    assert protected.risk is actuation.ActuationRisk.PROTECTED
    assert protected.approval_reason == "protected call parked: critical_entity"

    safe = actuation.effective_actuation_risk("homeassistant", "update_entity", None, None)
    assert safe.as_critical() is safe and not safe.critical


def test_the_promoted_reason_carries_no_label_or_value() -> None:
    reason = actuation.effective_actuation_risk(
        "light", "turn_on", {"entity_id": "light.secret_label_42"}, {"brightness_pct": 31}
    ).as_critical()
    assert "secret_label_42" not in reason.approval_reason
    assert "31" not in reason.approval_reason


async def test_the_registry_read_returns_the_owners_set() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"entity_id": "switch.freezer"}])
    assert await actuation.read_critical_registry(pool) == frozenset({"switch.freezer"})
    assert "critical_entities" in pool.fetch.await_args.args[0]


async def test_an_empty_registry_reads_as_empty_not_unreadable() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    assert await actuation.read_critical_registry(pool) == frozenset()


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("relation does not exist"),
        TimeoutError(),
        OSError("down"),
    ],
)
async def test_a_failed_registry_read_is_unreadable_never_empty(failure) -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=failure)
    assert await actuation.read_critical_registry(pool) is None


async def test_a_slow_registry_read_times_out_as_unreadable() -> None:
    import asyncio

    async def hang(*_args):
        await asyncio.sleep(5)

    pool = MagicMock()
    pool.fetch = hang
    assert await actuation.read_critical_registry(pool, deadline=0.01) is None


async def test_an_unexpected_row_shape_is_unreadable() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"entity_id": None}])
    assert await actuation.read_critical_registry(pool) is None
