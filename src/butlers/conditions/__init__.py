"""Evaluator seam for first-switch goal conditions (RFC 0038 S4); dark until .7 consumes it."""

from butlers.conditions.clock import resolve_clock
from butlers.conditions.model import (
    ClockReading,
    ConditionEvent,
    FireIntent,
    Grant,
    Instance,
    Receipt,
    Snapshot,
    State,
    Tri,
)
from butlers.conditions.predicate import PredicateError, validate
from butlers.conditions.service import (
    ConditionEvaluator,
    ConditionsUnavailable,
    Outcome,
    RegistrationRefused,
)

__all__ = [
    "ClockReading",
    "ConditionEvaluator",
    "ConditionEvent",
    "ConditionsUnavailable",
    "FireIntent",
    "Grant",
    "Instance",
    "Outcome",
    "PredicateError",
    "Receipt",
    "RegistrationRefused",
    "Snapshot",
    "State",
    "Tri",
    "resolve_clock",
    "validate",
]
