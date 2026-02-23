"""Switchboard pre-classification triage layer.

Deterministic rule evaluation that runs before LLM classification to reduce
unnecessary classification calls for high-signal personal-email workloads.

See docs/switchboard/pre_classification_triage.md for the full spec.
"""

from butlers.tools.switchboard.triage.cache import TriageRuleCache
from butlers.tools.switchboard.triage.evaluator import (
    TriageDecision,
    TriageEnvelope,
    evaluate_triage,
)
from butlers.tools.switchboard.triage.telemetry import (
    TriageTelemetry,
    get_triage_telemetry,
    reset_triage_telemetry_for_tests,
)

__all__ = [
    "TriageDecision",
    "TriageEnvelope",
    "TriageRuleCache",
    "TriageTelemetry",
    "evaluate_triage",
    "get_triage_telemetry",
    "reset_triage_telemetry_for_tests",
]
