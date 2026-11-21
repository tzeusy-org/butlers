"""Deterministic physical-risk and post-condition contracts for HA calls."""

from __future__ import annotations

import asyncio
import enum
import logging
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)


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
    critical: bool = False

    @property
    def approval_reason(self) -> str | None:
        """Bounded owner-visible categories, without values or arbitrary key text."""
        if not self.reasons:
            return None
        prefix = (
            "reversible call promoted"
            if self.base is ActuationRisk.REVERSIBLE
            else f"{self.base.value} call parked"
        )
        reason = f"{prefix}: " + ", ".join(self.reasons)
        if self.data_keys:
            reason += "; data keys: " + ", ".join(self.data_keys)
        if self.omitted_key_count:
            reason += f"; omitted data keys: {self.omitted_key_count}"
        return reason

    def as_critical(self) -> EffectiveActuationRisk:
        """At least ``consequential`` with the fixed ``critical_entity`` category.

        A ``protected`` call stays ``protected``; a ``safe`` read is never promoted
        (REQ-module-home-assistant-002).
        """
        if self.base is ActuationRisk.SAFE:
            return self
        risk = self.risk if self.risk.requires_approval else ActuationRisk.CONSEQUENTIAL
        reasons = (
            self.reasons if CRITICAL_REASON in self.reasons else (*self.reasons, CRITICAL_REASON)
        )
        return replace(self, risk=risk, reasons=reasons, critical=True)


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


# ---------------------------------------------------------------------------
# Critical entities (REQ-module-home-assistant-001/002, RFC 0028 Amendment 1)
# ---------------------------------------------------------------------------

#: The fixed park-reason category for a critical call. It never carries a label or a value.
CRITICAL_REASON = "critical_entity"
#: Nesting deeper than this (or a cycle, or an unreadable member list) counts as critical.
MAX_GROUP_DEPTH = 8
_INDIRECT_PAIRS = frozenset(
    {("scene", "turn_on"), ("script", "turn_on"), ("automation", "trigger")}
)

#: Reads a target's cached attributes; ``None`` means the entity is absent from Home's cache.
AttributeLookup = Callable[[str], Mapping[str, Any] | None]


def is_indirect_actuator(domain: str, service: str) -> bool:
    """A call Home cannot see through: it may move entities the call does not name.

    ``scene.turn_on``, ``script.turn_on``, any ``script.<name>``, ``automation.trigger``, every
    ``homeassistant`` service that is not ``safe``, and any pair this map does not list (so
    ``button.press`` and anything new fail closed instead of being enumerated).
    """
    pair = (domain, service)
    if classify_actuation(domain, service) is ActuationRisk.SAFE:
        return False
    return (
        domain == "script"
        or pair in _INDIRECT_PAIRS
        or domain == "homeassistant"
        or pair not in _RISK_MAP
    )


def _group_members(attributes: Mapping[str, Any]) -> list[str] | None:
    """The member ids of a group's ``entity_id`` attribute, or ``None`` if it is unreadable."""
    members = attributes["entity_id"]
    if (
        not isinstance(members, list)
        or not members
        or not all(isinstance(m, str) and _EXPLICIT_ENTITY_RE.fullmatch(m) for m in members)
    ):
        return None
    return list(members)


def _reaches_critical(
    entity_id: str,
    registry: frozenset[str],
    attributes: AttributeLookup,
    depth: int,
    trail: tuple[str, ...],
) -> bool:
    if entity_id in registry:
        return True
    if depth > MAX_GROUP_DEPTH or entity_id in trail:
        return True  # nesting too deep, or a cycle: Home cannot tell what it would move
    cached = attributes(entity_id)
    if cached is None:
        return True  # absent from the cache: Home cannot tell whether it is a group
    if "entity_id" not in cached:
        return False  # an ordinary entity, not a group
    members = _group_members(cached)
    if members is None:
        return True  # malformed member list
    return any(
        _reaches_critical(member, registry, attributes, depth + 1, (*trail, entity_id))
        for member in members
    )


def names_critical_entity(
    domain: str,
    service: str,
    target: dict[str, Any] | None,
    data: dict[str, Any] | None,
    *,
    registry: frozenset[str] | None,
    attributes: AttributeLookup,
) -> bool:
    """Whether a call must be treated as naming a critical entity.

    ``registry`` is the owner's critical set, or ``None`` when it could not be read, which
    fails closed: every call except a ``safe`` read names a critical entity. With an empty
    registry nothing changes. Otherwise a call is critical when it is an indirect actuator,
    when its target is not an explicit entity list, or when a target is critical itself, is
    missing from ``attributes``, or is a group whose members (followed through nested groups)
    are critical or cannot be read. ``attributes`` is the module's cache, not a fresh read.
    """
    if classify_actuation(domain, service) is ActuationRisk.SAFE:
        return False
    if registry is None:
        return True
    if not registry:
        return False
    if is_indirect_actuator(domain, service):
        return True
    entities = target_entity_ids(target)
    if not entities or DATA_TARGET_SELECTORS.intersection(data or {}):
        return True
    return any(_reaches_critical(entity, registry, attributes, 0, ()) for entity in entities)


#: Receipt statuses a replay returns without a request (a possible effect never needs attention).
SETTLED_WITHOUT_ATTENTION = frozenset({"succeeded", "already_satisfied"})
#: The pre-call read must finish no more than this long before the receipt claim (D8).
PRE_STATE_MAX_AGE_SECONDS = 10.0
#: Content-blind reasons a replay is refused before any receipt or request (REQ-003).
PRE_STATE_REFUSALS = frozenset({"pre_state_unavailable", "pre_state_stale", "target_unbound"})


def satisfied_by_pre_state(
    domain: str,
    service: str,
    data: dict[str, Any] | None,
    pre_state: dict[str, dict[str, Any]],
) -> bool:
    """Whether the live pre-call state already meets the call's declared post-condition.

    Only a service with a declared verifier can be satisfied. Extra ``data`` the verifier does not
    read (a light's brightness, a transition) is never compared, so a call carrying any is not
    declared satisfied: it proceeds to the normal attempt. ``climate.set_temperature`` is
    verified on its one ``temperature`` key.
    """
    if data and not (
        (domain, service) == ("climate", "set_temperature") and set(data) == {"temperature"}
    ):
        return False
    return verify_post_condition(domain, service, data, pre_state).verified


async def read_critical_registry(pool: Any, *, deadline: float = 2.0) -> frozenset[str] | None:
    """The owner's critical entity ids, read now; ``None`` when they cannot be read.

    A missing table, a query error, a timeout or an unexpected row shape is "unreadable", never
    "empty": the caller treats every call as naming a critical entity.
    """
    try:
        rows = await asyncio.wait_for(
            pool.fetch("SELECT entity_id FROM critical_entities"), deadline
        )
        entity_ids = [row["entity_id"] for row in rows]
        if not all(type(entity_id) is str for entity_id in entity_ids):
            return None
        return frozenset(entity_ids)
    except Exception:  # noqa: BLE001 - any failure to read the registry fails closed.
        logger.warning("home.critical_entities unreadable; treating every call as critical")
        return None


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
    "PRE_STATE_MAX_AGE_SECONDS",
    "PRE_STATE_REFUSALS",
    "SETTLED_WITHOUT_ATTENTION",
    "satisfied_by_pre_state",
    "CRITICAL_REASON",
    "MAX_GROUP_DEPTH",
    "ActuationRisk",
    "AttributeLookup",
    "Verification",
    "EffectiveActuationRisk",
    "DATA_TARGET_SELECTORS",
    "effective_actuation_risk",
    "is_indirect_actuator",
    "names_critical_entity",
    "read_critical_registry",
    "snapshot_actuation_arguments",
    "classify_actuation",
    "rollback_hint",
    "target_entity_ids",
    "verify_post_condition",
]


def communication_parties(
    domain: str, service: str, target: dict[str, Any] | None, data: dict[str, Any] | None
) -> tuple[bool, list[tuple[str, str]] | None]:
    """Separate recipient selectors from content without inferring device ownership.

    Existing identity handles use the ``other`` channel and fully qualified HA
    handles. Missing/area/group targets remain unresolved; a service name is not
    evidence of owner control. This adds no device-ownership registry.
    """
    communicates = domain in {"notify", "tts", "persistent_notification"} or service in {
        "send_message",
        "announce",
        "speak",
    }
    if not communicates:
        return False, []
    parties = []
    if domain == "notify":
        parties.append(("other", f"home_assistant:notify:{service}"))
    selectors = dict(target or {})
    payload = data or {}
    # Generic legacy notify services may forward data.target; other HA
    # selectors also cannot borrow a service handle's owner identity.
    if {"target", "area_id", "label_id", "floor_id"}.intersection(payload):
        return True, None
    for key in ("entity_id", "device_id", "media_player_entity_id"):
        if key in payload:
            if key in selectors:
                return True, None
            selectors[key] = payload[key]
    for key, values in selectors.items():
        if key not in {"entity_id", "device_id", "media_player_entity_id"}:
            return True, None
        values = [values] if isinstance(values, str) else values
        if not isinstance(values, list) or not values:
            return True, None
        for value in values:
            if not isinstance(value, str) or not value.strip() or value == "all":
                return True, None
            parties.append(("other", f"home_assistant:{key}:{value}"))
    return True, parties or None
