"""Deterministic wellness classifier for the Home Assistant connector.

Implements design ADR-1 of the ``home-assistant-wellness-promotion`` change: a
metadata-driven rule table that promotes health-shaped ``state_changed`` events
onto the ``wellness`` channel. No LLM is involved — classification is a pure
function of HA physical metadata (``device_class``, ``unit_of_measurement``,
and, for compound families, entity-id tokens). Vendor and integration names
NEVER appear in the rules: a Withings cuff, an ESPHome scale, and an Oura
bridge all classify identically.

Conservative by default — ambient-ambiguous signatures (``temperature``,
``humidity``, bare ``%``) are deliberately excluded because room sensors share
those signatures with body sensors, and a false positive writes a wrong health
fact. Owners extend the table via ``HA_WELLNESS_RULES_EXTRA`` (ADR-2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WellnessRule:
    """A single wellness-classification rule.

    A rule matches an event when every populated qualifier matches:

    - ``unit`` — exact match against ``unit_of_measurement`` (case-sensitive,
      matching HA's own unit strings, e.g. ``"mmHg"``, ``"mg/dL"``).
    - ``device_class`` — exact match against the entity's ``device_class``.
    - ``entity_token`` — case-insensitive substring of the ``entity_id``
      (used only for compound families like blood pressure where the unit
      alone is ambiguous between the systolic and diastolic legs).

    At least one qualifier must be set; ``metric`` is required and is the
    canonical metric name emitted downstream (predicate becomes
    ``measurement_{metric}`` on the Health side).
    """

    metric: str
    unit: str | None = None
    device_class: str | None = None
    entity_token: str | None = None

    def matches(
        self,
        *,
        entity_id: str,
        device_class: str | None,
        unit_of_measurement: str | None,
    ) -> bool:
        if self.unit is not None and unit_of_measurement != self.unit:
            return False
        if self.device_class is not None and device_class != self.device_class:
            return False
        if self.entity_token is not None and self.entity_token.lower() not in entity_id.lower():
            return False
        return True


# ---------------------------------------------------------------------------
# Default rule table (ADR-1)
# ---------------------------------------------------------------------------

DEFAULT_WELLNESS_RULES: tuple[WellnessRule, ...] = (
    # Blood pressure — mmHg is ambiguous, so the entity token disambiguates
    # the systolic/diastolic legs (ADR-6: stored as two facts).
    WellnessRule(metric="blood_pressure_systolic", unit="mmHg", entity_token="systolic"),
    WellnessRule(metric="blood_pressure_diastolic", unit="mmHg", entity_token="diastolic"),
    # Weight — require the weight device_class so a bare kg/lb sensor (e.g. a
    # parcel scale) does not get promoted.
    WellnessRule(metric="weight", device_class="weight", unit="kg"),
    WellnessRule(metric="weight", device_class="weight", unit="lb"),
    # Heart rate.
    WellnessRule(metric="heart_rate", unit="bpm"),
    # Blood glucose.
    WellnessRule(metric="blood_sugar", unit="mg/dL"),
    WellnessRule(metric="blood_sugar", unit="mmol/L"),
    # Steps.
    WellnessRule(metric="steps", unit="steps"),
)


# Sentinel non-numeric HA states.
_NON_NUMERIC_STATES = frozenset({"", "unknown", "unavailable", "none", "null"})


ClassifyOutcome = Literal["promoted", "no_match", "skipped_non_numeric", "denylisted"]


@dataclass(frozen=True)
class ClassifyResult:
    """Full classifier verdict for observability.

    ``metric`` is non-``None`` only when ``outcome == "promoted"``. ``value`` is
    the parsed numeric state for promoted readings.
    """

    metric: str | None
    outcome: ClassifyOutcome
    value: float | None = None


def _parse_numeric(state: str | None) -> float | None:
    """Return the numeric value of a HA state string, or ``None`` if non-numeric."""
    if state is None:
        return None
    text = state.strip()
    if not text or text.lower() in _NON_NUMERIC_STATES:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class WellnessClassifier:
    """Classify HA ``state_changed`` events into wellness metrics.

    The classifier is a pure object: it holds the rule table and denylist and
    has no I/O. ``extra_rules`` (from ``HA_WELLNESS_RULES_EXTRA``) are appended
    to the default table and take precedence on ties (they are evaluated
    first), letting an owner override or extend the defaults without code.
    """

    def __init__(
        self,
        *,
        extra_rules: tuple[WellnessRule, ...] = (),
        denylist: frozenset[str] = frozenset(),
    ) -> None:
        # Extra rules first so an owner override wins over a default match.
        self._rules: tuple[WellnessRule, ...] = tuple(extra_rules) + DEFAULT_WELLNESS_RULES
        self._denylist = denylist

    def classify(
        self,
        *,
        entity_id: str,
        device_class: str | None,
        unit_of_measurement: str | None,
        attributes: dict[str, Any],
        state: str | None,
    ) -> str | None:
        """Return the matched metric name, or ``None`` if not promoted.

        Pure function of ``(entity_id, device_class, unit_of_measurement,
        attributes, state)``. Non-numeric states and denylisted entities
        return ``None``.
        """
        return self.classify_detailed(
            entity_id=entity_id,
            device_class=device_class,
            unit_of_measurement=unit_of_measurement,
            attributes=attributes,
            state=state,
        ).metric

    def classify_detailed(
        self,
        *,
        entity_id: str,
        device_class: str | None,
        unit_of_measurement: str | None,
        attributes: dict[str, Any],
        state: str | None,
    ) -> ClassifyResult:
        """Return the full classification verdict (metric + outcome + value)."""
        if entity_id in self._denylist:
            return ClassifyResult(metric=None, outcome="denylisted")

        matched: WellnessRule | None = None
        for rule in self._rules:
            if rule.matches(
                entity_id=entity_id,
                device_class=device_class,
                unit_of_measurement=unit_of_measurement,
            ):
                matched = rule
                break

        if matched is None:
            return ClassifyResult(metric=None, outcome="no_match")

        value = _parse_numeric(state)
        if value is None:
            return ClassifyResult(metric=None, outcome="skipped_non_numeric")

        return ClassifyResult(metric=matched.metric, outcome="promoted", value=value)


# ---------------------------------------------------------------------------
# Config parsing helpers (ADR-2)
# ---------------------------------------------------------------------------


def parse_rules_extra(raw: str) -> tuple[WellnessRule, ...]:
    """Parse the ``HA_WELLNESS_RULES_EXTRA`` JSON into ``WellnessRule`` objects.

    The value is a JSON list of objects with keys
    ``{device_class?, unit?, entity_token?, metric}``. ``metric`` is required;
    at least one qualifier must be present.

    Raises:
        ValueError: On malformed JSON, wrong top-level type, a missing
            ``metric``, or a rule with no qualifier — with a clear,
            actionable message naming the env var.
    """
    if not raw.strip():
        return ()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"HA_WELLNESS_RULES_EXTRA is not valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ValueError("HA_WELLNESS_RULES_EXTRA must be a JSON list of rule objects")

    rules: list[WellnessRule] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(
                f"HA_WELLNESS_RULES_EXTRA[{i}] must be a JSON object, got {type(item).__name__}"
            )
        metric = item.get("metric")
        if not metric or not isinstance(metric, str):
            raise ValueError(f"HA_WELLNESS_RULES_EXTRA[{i}] is missing a non-empty string 'metric'")
        unit = item.get("unit")
        device_class = item.get("device_class")
        entity_token = item.get("entity_token")
        if unit is None and device_class is None and entity_token is None:
            raise ValueError(
                f"HA_WELLNESS_RULES_EXTRA[{i}] must set at least one of "
                "'unit', 'device_class', or 'entity_token'"
            )
        rules.append(
            WellnessRule(
                metric=metric,
                unit=unit,
                device_class=device_class,
                entity_token=entity_token,
            )
        )
    return tuple(rules)
