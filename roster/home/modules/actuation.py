"""Deterministic physical-risk and post-condition contracts for HA calls."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class ActuationRisk(enum.StrEnum):
    SAFE = "safe"
    REVERSIBLE = "reversible"
    CONSEQUENTIAL = "consequential"
    PROTECTED = "protected"

    @property
    def requires_approval(self) -> bool:
        return self in {self.CONSEQUENTIAL, self.PROTECTED}


# The map is deliberately allowlisted. Unknown services cross the physical-world
# boundary as protected until an owner-reviewed classification is added here.
_RISK_MAP: dict[tuple[str, str], ActuationRisk] = {
    ("homeassistant", "update_entity"): ActuationRisk.SAFE,
    ("light", "turn_on"): ActuationRisk.REVERSIBLE,
    ("light", "turn_off"): ActuationRisk.REVERSIBLE,
    ("switch", "turn_on"): ActuationRisk.REVERSIBLE,
    ("switch", "turn_off"): ActuationRisk.REVERSIBLE,
    ("fan", "turn_on"): ActuationRisk.REVERSIBLE,
    ("fan", "turn_off"): ActuationRisk.REVERSIBLE,
    ("humidifier", "turn_on"): ActuationRisk.REVERSIBLE,
    ("humidifier", "turn_off"): ActuationRisk.REVERSIBLE,
    ("climate", "set_temperature"): ActuationRisk.REVERSIBLE,
    ("cover", "close_cover"): ActuationRisk.CONSEQUENTIAL,
    ("cover", "open_cover"): ActuationRisk.PROTECTED,
    ("cover", "toggle"): ActuationRisk.PROTECTED,
    ("scene", "turn_on"): ActuationRisk.CONSEQUENTIAL,
    ("script", "turn_on"): ActuationRisk.CONSEQUENTIAL,
    ("automation", "trigger"): ActuationRisk.CONSEQUENTIAL,
    ("lock", "lock"): ActuationRisk.PROTECTED,
    ("lock", "unlock"): ActuationRisk.PROTECTED,
    ("lock", "open"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_arm_away"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_arm_home"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_arm_night"): ActuationRisk.PROTECTED,
    ("alarm_control_panel", "alarm_disarm"): ActuationRisk.PROTECTED,
    ("homeassistant", "turn_on"): ActuationRisk.CONSEQUENTIAL,
    ("homeassistant", "turn_off"): ActuationRisk.CONSEQUENTIAL,
    ("homeassistant", "toggle"): ActuationRisk.PROTECTED,
}

_EXPECTED_STATES: dict[tuple[str, str], str] = {
    ("light", "turn_on"): "on",
    ("light", "turn_off"): "off",
    ("switch", "turn_on"): "on",
    ("switch", "turn_off"): "off",
    ("fan", "turn_on"): "on",
    ("fan", "turn_off"): "off",
    ("humidifier", "turn_on"): "on",
    ("humidifier", "turn_off"): "off",
    ("cover", "open_cover"): "open",
    ("cover", "close_cover"): "closed",
    ("lock", "lock"): "locked",
    ("lock", "unlock"): "unlocked",
}

_INVERSE_SERVICES: dict[tuple[str, str], str] = {
    ("light", "turn_on"): "turn_off",
    ("light", "turn_off"): "turn_on",
    ("switch", "turn_on"): "turn_off",
    ("switch", "turn_off"): "turn_on",
    ("fan", "turn_on"): "turn_off",
    ("fan", "turn_off"): "turn_on",
    ("humidifier", "turn_on"): "turn_off",
    ("humidifier", "turn_off"): "turn_on",
}


def classify_actuation(domain: str, service: str) -> ActuationRisk:
    """Return the declared risk, defaulting unknown physical calls to protected."""
    return _RISK_MAP.get((domain, service), ActuationRisk.PROTECTED)


def target_entity_ids(target: dict[str, Any] | None) -> list[str]:
    """Extract explicit entity targets; area/device targets are not guessable."""
    if not target:
        return []
    raw = target.get("entity_id")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return raw
    return []


def rollback_hint(
    domain: str,
    service: str,
    target: dict[str, Any] | None,
    data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a concrete inverse for actions classified as reversible."""
    inverse = _INVERSE_SERVICES.get((domain, service))
    if inverse is not None:
        return {"domain": domain, "service": inverse, "target": target, "data": None}
    if (domain, service) == ("climate", "set_temperature"):
        return {
            "domain": domain,
            "service": service,
            "target": target,
            "data": {"temperature": "restore_previous_observed_temperature"},
        }
    return None


@dataclass(frozen=True, slots=True)
class Verification:
    verified: bool
    reason: str | None


def verify_post_condition(
    domain: str,
    service: str,
    data: dict[str, Any] | None,
    observed: dict[str, dict[str, Any]],
) -> Verification:
    """Compare live post-call entity states with the service's declared effect."""
    if not observed:
        return Verification(False, "no explicit entity target was available for verification")

    expected_state = _EXPECTED_STATES.get((domain, service))
    if expected_state is not None:
        mismatches = [
            entity_id
            for entity_id, state in observed.items()
            if state.get("state") != expected_state
        ]
        if mismatches:
            return Verification(
                False,
                f"post-condition mismatch for {', '.join(sorted(mismatches))}: "
                f"expected state {expected_state!r}",
            )
        return Verification(True, None)

    if (domain, service) == ("climate", "set_temperature"):
        requested = (data or {}).get("temperature")
        if requested is None:
            return Verification(False, "set_temperature omitted the requested temperature")
        mismatches = [
            entity_id
            for entity_id, state in observed.items()
            if state.get("attributes", {}).get("temperature") != requested
        ]
        if mismatches:
            return Verification(
                False,
                f"post-condition mismatch for {', '.join(sorted(mismatches))}: "
                f"expected temperature {requested!r}",
            )
        return Verification(True, None)

    return Verification(
        False, f"no deterministic post-condition is declared for {domain}.{service}"
    )


__all__ = [
    "ActuationRisk",
    "Verification",
    "classify_actuation",
    "rollback_hint",
    "target_entity_ids",
    "verify_post_condition",
]
