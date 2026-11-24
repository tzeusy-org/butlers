"""RFC 0028 risk map adapted into broker actuate dispositions (bu-m1u7zy.11)."""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from butlers.capability_broker.actuation import critical_actuation_risk, rfc0028_actuation_risk
from butlers.capability_broker.contracts import Capability, Decision, Disposition
from butlers.capability_broker.policy import decide
from butlers.capability_broker.recipients import classify_recipients
from butlers.capability_broker.service import Broker
from butlers.core.goal_admission import Grant
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME
from tests.capability_broker.helpers import capability, principal

pytestmark = pytest.mark.unit

HA_CALL = capability("ha_call_service", "actuate", "home")
HA_SCENE = capability("ha_activate_scene", "actuate", "home")
LIGHT_ON = {
    "domain": "light",
    "service": "turn_on",
    "target": {"entity_id": "light.kitchen"},
    "data": {"brightness_pct": 40},
}


@pytest.mark.parametrize(
    ("cap", "args", "risk"),
    [
        # Reversible within RFC 0028's automatic subset, and the read-only refresh.
        (HA_CALL, LIGHT_ON, "safe"),
        (HA_CALL, {"domain": "homeassistant", "service": "update_entity"}, "safe"),
        # Reversible promoted: bulk target, unlisted data, or a unit the broker lacks.
        (HA_CALL, {**LIGHT_ON, "target": {"area_id": "kitchen"}}, "consequential"),
        (HA_CALL, {**LIGHT_ON, "data": {"flash": "long"}}, "consequential"),
        (
            HA_CALL,
            {
                "domain": "climate",
                "service": "set_temperature",
                "target": {"entity_id": "climate.hall"},
                "data": {"temperature": 21},
            },
            "consequential",
        ),
        # Declared consequential and protected services.
        (HA_CALL, {"domain": "script", "service": "turn_on"}, "consequential"),
        (HA_CALL, {"domain": "lock", "service": "unlock"}, "protected"),
        (HA_SCENE, {"entity_id": "scene.movie_night"}, "consequential"),
        # RFC 0028: an undeclared service is protected.
        (HA_CALL, {"domain": "vacuum", "service": "start"}, "protected"),
        # Unreadable calls and capabilities outside the map are unknown.
        (HA_CALL, {"domain": "light"}, "unknown"),
        (HA_CALL, {**LIGHT_ON, "target": "light.kitchen"}, "unknown"),
        (HA_SCENE, {"entity_id": "light.kitchen"}, "unknown"),
        (capability("spotify_play", "actuate", "music"), {}, "unknown"),
    ],
)
def test_actuate_calls_read_the_rfc0028_risk_map(cap, args, risk):
    assert rfc0028_actuation_risk(cap, args) == risk


def _decide(cap, args, *, approval_gate=False):
    cap = capability(cap.name, "actuate", cap.domain, approval_gate=approval_gate)
    grant = Grant(cap.identity)
    return decide(principal(grant), cap, grant, "none", rfc0028_actuation_risk(cap, args))


def test_declared_low_risk_actuation_follows_its_approval_policy():
    """Policy alone, for a synthetic capability with no declared parties."""
    assert _decide(HA_CALL, LIGHT_ON) == Decision(Disposition.DISPATCH)
    gated = _decide(HA_CALL, LIGHT_ON, approval_gate=True)
    assert gated == Decision(Disposition.PARK, allow_standing_rule=True)


async def test_a_safe_catalog_ha_call_parks_rule_eligible_on_undeterminable_recipients():
    """Today's catalog declares ``ha_call_service`` recipients undeterminable.

    So the real descriptor never reaches the dispatch branch above: even a safe
    light call names a party the broker cannot rule out, and parks rule-eligible.
    """
    declared = TOOL_PRESENTATION_BY_NAME["ha_call_service"]
    # The inventory declaration, as the published descriptor carries it.
    descriptor = SimpleNamespace(
        **{item.name: getattr(declared, item.name) for item in fields(declared)},
        description="",
    )
    cap = Capability.from_descriptor("home", descriptor, {"type": "object"})
    assert (cap.effect_class, cap.recipient_params.undeterminable) == ("actuate", True)
    risk = rfc0028_actuation_risk(cap, LIGHT_ON)
    assert risk == "safe"

    # The undeterminable branch decides before any owner lookup.
    recipient = await classify_recipients(None, cap, LIGHT_ON)
    assert recipient == "non_owner"
    grant = Grant(cap.identity)
    decision = decide(principal(grant), cap, grant, recipient, risk)
    assert decision == Decision(Disposition.PARK, allow_standing_rule=True)


@pytest.mark.parametrize(
    ("cap", "args"),
    [
        (HA_CALL, {"domain": "vacuum", "service": "start"}),  # undeclared service
        (capability("spotify_play", "actuate", "music"), {}),  # undeclared capability
        (HA_CALL, {"domain": "lock", "service": "unlock"}),  # protected
        (HA_SCENE, {"entity_id": "scene.movie_night"}),  # consequential
    ],
)
def test_undeclared_or_risky_actuation_stays_parked_per_event(cap, args):
    assert _decide(cap, args) == Decision(Disposition.PARK, allow_standing_rule=False)


def test_the_broker_reads_the_rfc0028_map_by_default():
    assert Broker(None, None, None, None).actuation_risk is rfc0028_actuation_risk


# --- Critical entities (bu-jv1moa.122, REQ-module-home-assistant-002) -----------------------


def _registry(*entity_ids):
    async def read():
        return frozenset(entity_ids)

    return read


async def _critical_risk(cap, args, reader):
    return await critical_actuation_risk(rfc0028_actuation_risk(cap, args), cap, args, reader)


async def test_a_goal_call_naming_a_critical_entity_is_consequential_and_parks_per_event():
    risk = await _critical_risk(HA_CALL, LIGHT_ON, _registry("light.kitchen"))
    assert risk == "consequential"
    cap = capability("ha_call_service", "actuate", "home", approval_gate=True)
    grant = Grant(cap.identity)
    decision = decide(principal(grant), cap, grant, "none", risk)
    assert decision == Decision(Disposition.PARK, allow_standing_rule=False)


async def test_an_empty_or_unrelated_registry_leaves_the_tier_alone():
    assert await _critical_risk(HA_CALL, LIGHT_ON, _registry()) == "safe"
    assert await _critical_risk(HA_CALL, LIGHT_ON, _registry("switch.freezer")) == "safe"


@pytest.mark.parametrize(
    "args",
    [
        {**LIGHT_ON, "target": {"area_id": "kitchen"}},
        {"domain": "scene", "service": "turn_on"},
        {"domain": "button", "service": "press", "target": {"entity_id": "button.x"}},
    ],
)
async def test_broad_and_indirect_goal_calls_stay_parked_while_the_registry_is_non_empty(args):
    assert await _critical_risk(HA_CALL, args, _registry("switch.freezer")) in {
        "consequential",
        "protected",
    }


async def test_a_scene_naming_a_critical_entity_never_gets_cheaper():
    args = {"entity_id": "scene.movie_night"}
    assert await _critical_risk(HA_SCENE, args, _registry("light.kitchen")) == "consequential"


async def test_an_unreadable_registry_makes_every_home_call_unknown():
    async def failing():
        raise RuntimeError("table missing")

    async def hanging():
        import asyncio

        await asyncio.sleep(60)

    async def malformed():
        return None

    for reader in (None, failing, malformed):
        assert await _critical_risk(HA_CALL, LIGHT_ON, reader) == "unknown"
        assert await _critical_risk(HA_SCENE, {"entity_id": "scene.x"}, reader) == "unknown"
    # A reader that never answers is cut off, not awaited.
    import butlers.capability_broker.actuation as adapter

    original = adapter._REGISTRY_TIMEOUT
    adapter._REGISTRY_TIMEOUT = 0.01
    try:
        assert await _critical_risk(HA_CALL, LIGHT_ON, hanging) == "unknown"
    finally:
        adapter._REGISTRY_TIMEOUT = original


async def test_a_safe_read_and_non_home_capabilities_are_never_promoted():
    refresh = {"domain": "homeassistant", "service": "update_entity"}
    for reader in (None, _registry("light.kitchen")):
        assert await _critical_risk(HA_CALL, refresh, reader) == "safe"
    other = capability("spotify_play", "actuate", "music")
    assert await _critical_risk(other, {}, None) == "unknown"


async def test_the_broker_carries_the_registry_reader_and_defaults_to_none():
    """No reader means a Home call is unknown and parks per event (see the cases above)."""
    broker = Broker(None, None, None, None)
    assert broker.critical_registry is None
    wired = Broker(None, None, None, None, critical_registry=_registry("light.kitchen"))
    assert await wired.critical_registry() == frozenset({"light.kitchen"})
