"""Switchboard pre-classification triage layer.

Deterministic rule evaluation that runs before LLM classification to reduce
unnecessary classification calls for high-signal personal-email workloads.

See docs/switchboard/pre_classification_triage.md for the full spec.
See docs/switchboard/thread_affinity_routing.md for thread affinity spec.
"""

from butlers.tools.switchboard.triage.cache import TriageRuleCache
from butlers.tools.switchboard.triage.evaluator import (
    TriageDecision,
    TriageEnvelope,
    evaluate_triage,
)
from butlers.tools.switchboard.triage.telemetry import (
    ThreadAffinityTelemetry,
    TriageTelemetry,
    get_thread_affinity_telemetry,
    get_triage_telemetry,
    reset_thread_affinity_telemetry_for_tests,
    reset_triage_telemetry_for_tests,
)
from butlers.tools.switchboard.triage.thread_affinity import (
    AffinityOutcome,
    AffinityResult,
    ThreadAffinitySettings,
    load_settings,
    lookup_thread_affinity,
)

__all__ = [
    "AffinityOutcome",
    "AffinityResult",
    "ThreadAffinitySettings",
    "ThreadAffinityTelemetry",
    "TriageDecision",
    "TriageEnvelope",
    "TriageRuleCache",
    "TriageTelemetry",
    "evaluate_triage",
    "get_thread_affinity_telemetry",
    "get_triage_telemetry",
    "load_settings",
    "lookup_thread_affinity",
    "reset_thread_affinity_telemetry_for_tests",
    "reset_triage_telemetry_for_tests",
]
