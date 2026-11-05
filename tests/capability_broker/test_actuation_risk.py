"""RFC 0028 risk map adapted into broker actuate dispositions (bu-m1u7zy.11)."""

from dataclasses import fields
from types import SimpleNamespace

import pytest

from butlers.capability_broker.actuation import rfc0028_actuation_risk
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
