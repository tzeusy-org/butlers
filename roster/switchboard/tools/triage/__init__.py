"""Switchboard thread-affinity routing layer.

See docs/switchboard/thread_affinity_routing.md for the full spec.
"""

from butlers.tools.switchboard.triage.thread_affinity import (
    AffinityOutcome,
    AffinityResult,
    ThreadAffinitySettings,
    ThreadAffinityTelemetry,
    get_thread_affinity_telemetry,
    load_settings,
    lookup_thread_affinity,
    reset_thread_affinity_telemetry_for_tests,
)

__all__ = [
    "AffinityOutcome",
    "AffinityResult",
    "ThreadAffinitySettings",
    "ThreadAffinityTelemetry",
    "get_thread_affinity_telemetry",
    "load_settings",
    "lookup_thread_affinity",
    "reset_thread_affinity_telemetry_for_tests",
]
