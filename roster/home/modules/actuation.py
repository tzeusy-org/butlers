"""Deterministic physical-risk and post-condition contracts for HA calls."""

from __future__ import annotations

import enum
import math
import re
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
    """Observe only an entirely explicit entity target, never a partial bulk target."""
    if not target or set(target) != {"entity_id"}:
        return []
    raw = target.get("entity_id")
    entities = [raw] if isinstance(raw, str) else raw
    if (
        isinstance(entities, list)
        and entities
        and all(isinstance(item, str) and _EXPLICIT_ENTITY_RE.fullmatch(item) for item in entities)
    ):
        return list(entities)
    return []


_EXPLICIT_ENTITY_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_DIAGNOSTIC_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
DATA_TARGET_SELECTORS = frozenset({"entity_id", "area_id", "device_id", "label_id", "target"})
_LIGHT_ON_KEYS = frozenset(
    {"brightness", "brightness_pct", "color_temp_kelvin", "rgb_color", "transition"}
)


def snapshot_actuation_arguments(
    target: dict[str, Any] | None, data: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Detach exact JSON arguments before any await, rejecting unpersistable values.

    Do not coerce numbers, stringify objects or turn None into an empty object:
    the same snapshot must bind approval, classification, receipts and transport.
    """
    active: set[int] = set()

    def clone(value: Any) -> Any:
        if type(value) is str:
            if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("Home Assistant argument strings must be representable in JSONB")
            return value
        if value is None or type(value) in (bool, int):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if type(value) not in (dict, list) or id(value) in active:
            raise ValueError("Home Assistant arguments must be finite, acyclic JSON")
        active.add(id(value))
        try:
            if type(value) is dict:
                if any(type(key) is not str for key in value):
                    raise ValueError("Home Assistant argument object keys must be strings")
                return {clone(key): clone(item) for key, item in value.items()}
            return [clone(item) for item in value]
        finally:
            active.remove(id(value))

    if any(value is not None and type(value) is not dict for value in (target, data)):
        raise ValueError("Home Assistant target and data must be JSON objects or null")
    try:
        return clone(target), clone(data)
    except RecursionError:
        raise ValueError("Home Assistant arguments exceed JSON nesting support") from None


@dataclass(frozen=True, slots=True)
class EffectiveActuationRisk:
    base: ActuationRisk
    risk: ActuationRisk
    reasons: tuple[str, ...] = ()
    data_keys: tuple[str, ...] = ()
    omitted_key_count: int = 0

    @property
    def approval_reason(self) -> str | None:
        """Bounded owner-visible categories, without values or arbitrary key text."""
        if not self.reasons:
            return None
        reason = "reversible call promoted: " + ", ".join(self.reasons)
        if self.data_keys:
            reason += "; data keys: " + ", ".join(self.data_keys)
        if self.omitted_key_count:
            reason += f"; omitted data keys: {self.omitted_key_count}"
        return reason


def _bounded_number(value: Any, low: float, high: float, *, integer: bool = False) -> bool:
    if type(value) is int:
        return low <= value <= high
    return not integer and type(value) is float and math.isfinite(value) and low <= value <= high


def effective_actuation_risk(
    domain: str,
    service: str,
    target: dict[str, Any] | None,
    data: dict[str, Any] | None,
    *,
    temperature_units: dict[str, Any] | None = None,
) -> EffectiveActuationRisk:
    """Only promote reversible services beyond the conservative automatic subset.

    These are local auto-execution limits, not a vendor schema or an assertion
    that an entity's cached unit is available or fresh. Unknown units park.
    Callers provide a validated detached JSON snapshot and module-owned units.
    """
    base = classify_actuation(domain, service)
    if base is not ActuationRisk.REVERSIBLE:
        return EffectiveActuationRisk(base, base)
    reasons: list[str] = []
    entities = target_entity_ids(target)
    if not entities:
        reasons.append("target_not_explicit_entities")
    elif any(entity.split(".", 1)[0] != domain for entity in entities):
        reasons.append("target_domain_mismatch")

    values = data if data is not None else {}
    if DATA_TARGET_SELECTORS.intersection(values):
        reasons.append("data_target_selector")
    allowed = frozenset()
    if (domain, service) == ("light", "turn_on"):
        allowed = _LIGHT_ON_KEYS
    elif (domain, service) == ("light", "turn_off"):
        allowed = frozenset({"transition"})
    elif (domain, service) == ("climate", "set_temperature"):
        allowed = frozenset({"temperature"})
    unknown = set(values) - allowed
    if unknown:
        reasons.append("unlisted_data_key")

    for key, value in values.items():
        valid = True
        if key == "brightness" and key in allowed:
            valid = _bounded_number(value, 0, 255, integer=True)
        elif key == "brightness_pct" and key in allowed:
            valid = _bounded_number(value, 0, 100)
        elif key == "color_temp_kelvin" and key in allowed:
            valid = _bounded_number(value, 1000, 10000, integer=True)
        elif key == "transition" and key in allowed:
            valid = _bounded_number(value, 0, 60)
        elif key == "rgb_color" and key in allowed:
            valid = (
                type(value) is list
                and len(value) == 3
                and all(_bounded_number(item, 0, 255, integer=True) for item in value)
            )
        if not valid and "data_type_or_bounds" not in reasons:
            reasons.append("data_type_or_bounds")
    if domain == "light" and (
        {"brightness", "brightness_pct"} <= set(values)
        or {"rgb_color", "color_temp_kelvin"} <= set(values)
    ):
        reasons.append("incompatible_data_keys")

    if (domain, service) == ("climate", "set_temperature"):
        units = temperature_units or {}
        normalized = [
            {"C": "C", "°C": "C", "F": "F", "°F": "F"}.get(units.get(e))
            if isinstance(units.get(e), str)
            else None
            for e in entities
        ]
        if not normalized or None in normalized or len(set(normalized)) != 1:
            reasons.append("temperature_unit_unavailable_or_mixed")
        else:
            low, high = (16, 28) if normalized[0] == "C" else (60.8, 82.4)
            if not _bounded_number(values.get("temperature"), low, high):
                reasons.append("temperature_type_or_comfort_bounds")

    keys = tuple(sorted(key for key in unknown if _DIAGNOSTIC_KEY_RE.fullmatch(key))[:8])
    return EffectiveActuationRisk(
        base,
        ActuationRisk.CONSEQUENTIAL if reasons else base,
        tuple(reasons),
        keys,
        len(unknown) - len(keys),
    )


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
    "EffectiveActuationRisk",
    "DATA_TARGET_SELECTORS",
    "effective_actuation_risk",
    "snapshot_actuation_arguments",
    "classify_actuation",
    "rollback_hint",
    "target_entity_ids",
    "verify_post_condition",
]
