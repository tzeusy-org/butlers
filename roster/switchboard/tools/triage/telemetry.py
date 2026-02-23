"""Triage metrics for the pre-classification triage layer.

Implements the telemetry contract from docs/switchboard/pre_classification_triage.md §8.

Metrics:
  1. butlers.switchboard.triage.rule_matched (counter)
     - Attributes: rule_type, action, source_channel
  2. butlers.switchboard.triage.pass_through (counter)
     - Attributes: source_channel, reason (no_match|cache_unavailable|rules_disabled)
  3. butlers.switchboard.triage.evaluation_latency_ms (histogram)
     - Attributes: result (matched|pass_through|error)

Cardinality policy (spec §8):
  - MUST NOT include raw email addresses, domains, thread IDs, or request IDs.
  - Attribute values are bounded to known-good sets.
"""

from __future__ import annotations

from opentelemetry import metrics

_METER_NAME = "butlers.switchboard"

# Allowed attribute values for cardinality control
_ALLOWED_RULE_TYPES = frozenset(
    {"sender_domain", "sender_address", "header_condition", "mime_type", "thread_affinity"}
)
_ALLOWED_ACTIONS = frozenset(
    {"skip", "metadata_only", "low_priority_queue", "pass_through", "route_to"}
)
_ALLOWED_PASS_THROUGH_REASONS = frozenset({"no_match", "cache_unavailable", "rules_disabled"})
_ALLOWED_RESULTS = frozenset({"matched", "pass_through", "error"})


def _safe_action(action: str) -> str:
    """Normalize action string to a low-cardinality label.

    route_to:finance → route_to (strips target to avoid cardinality blowup)
    """
    if action.startswith("route_to:"):
        return "route_to"
    if action in _ALLOWED_ACTIONS:
        return action
    return "unknown"


def _safe_rule_type(rule_type: str | None) -> str:
    if rule_type in _ALLOWED_RULE_TYPES:
        return str(rule_type)
    return "unknown"


def _safe_reason(reason: str | None) -> str:
    if reason in _ALLOWED_PASS_THROUGH_REASONS:
        return str(reason)
    return "no_match"


def _safe_result(result: str | None) -> str:
    if result in _ALLOWED_RESULTS:
        return str(result)
    return "unknown"


class TriageTelemetry:
    """Container for triage-specific OpenTelemetry metrics.

    All attribute values are sanitized to enforce low-cardinality contracts.
    Raw email addresses, domains, request IDs, and thread IDs are NEVER
    included as metric attributes.
    """

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)

        # Counter: incremented when any rule (or thread affinity) matches.
        self.rule_matched = meter.create_counter(
            "butlers.switchboard.triage.rule_matched",
            unit="1",
            description=(
                "Number of messages matched by a deterministic triage rule "
                "(including thread affinity)."
            ),
        )

        # Counter: incremented only when no deterministic match occurs.
        self.pass_through = meter.create_counter(
            "butlers.switchboard.triage.pass_through",
            unit="1",
            description=(
                "Number of messages that passed through to LLM classification "
                "without a deterministic triage match."
            ),
        )

        # Histogram: end-to-end triage evaluation latency.
        self.evaluation_latency_ms = meter.create_histogram(
            "butlers.switchboard.triage.evaluation_latency_ms",
            unit="ms",
            description="End-to-end triage evaluation latency in milliseconds.",
        )

    def record_rule_matched(
        self,
        *,
        rule_type: str,
        action: str,
        source_channel: str,
    ) -> None:
        """Record a successful rule match.

        Parameters are sanitized for low-cardinality compliance.
        """
        self.rule_matched.add(
            1,
            {
                "rule_type": _safe_rule_type(rule_type),
                "action": _safe_action(action),
                "source_channel": str(source_channel)[:32] if source_channel else "unknown",
            },
        )

    def record_pass_through(
        self,
        *,
        source_channel: str,
        reason: str,
    ) -> None:
        """Record a pass-through (no deterministic match).

        reason must be one of: no_match, cache_unavailable, rules_disabled.
        """
        self.pass_through.add(
            1,
            {
                "source_channel": str(source_channel)[:32] if source_channel else "unknown",
                "reason": _safe_reason(reason),
            },
        )

    def record_evaluation_latency(
        self,
        *,
        latency_ms: float,
        result: str,
    ) -> None:
        """Record end-to-end triage evaluation latency.

        result must be one of: matched, pass_through, error.
        """
        self.evaluation_latency_ms.record(
            latency_ms,
            {"result": _safe_result(result)},
        )


_TRIAGE_TELEMETRY: TriageTelemetry | None = None


def get_triage_telemetry() -> TriageTelemetry:
    """Return the process-level triage telemetry singleton."""
    global _TRIAGE_TELEMETRY
    if _TRIAGE_TELEMETRY is None:
        _TRIAGE_TELEMETRY = TriageTelemetry()
    return _TRIAGE_TELEMETRY


def reset_triage_telemetry_for_tests() -> None:
    """Test helper to reset the triage telemetry singleton after meter changes."""
    global _TRIAGE_TELEMETRY
    _TRIAGE_TELEMETRY = None


# ---------------------------------------------------------------------------
# Thread affinity telemetry
# ---------------------------------------------------------------------------

# Allowed miss reason values for thread affinity
_ALLOWED_AFFINITY_MISS_REASONS = frozenset(
    {"no_thread_id", "no_history", "conflict", "disabled", "error", "stale"}
)


class ThreadAffinityTelemetry:
    """OpenTelemetry metrics for thread-affinity routing lookups.

    Implements the metric contract from docs/switchboard/thread_affinity_routing.md §7.

    Metrics:
      - butlers.switchboard.thread_affinity.hit (counter)
      - butlers.switchboard.thread_affinity.miss (counter, with reason attribute)
      - butlers.switchboard.thread_affinity.stale (counter)

    Cardinality policy (spec §7):
      - MUST NOT include raw thread_id values.
      - reason attribute is bounded to known-good values.
      - destination_butler on hit is low-cardinality (butler names are bounded).
    """

    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)

        self.hit = meter.create_counter(
            "butlers.switchboard.thread_affinity.hit",
            unit="1",
            description=(
                "Number of email thread affinity lookups that produced a routing decision "
                "without LLM classification."
            ),
        )

        self.miss = meter.create_counter(
            "butlers.switchboard.thread_affinity.miss",
            unit="1",
            description=(
                "Number of email thread affinity lookups that did not produce a route "
                "and fell through to LLM classification."
            ),
        )

        self.stale = meter.create_counter(
            "butlers.switchboard.thread_affinity.stale",
            unit="1",
            description=(
                "Number of email threads where historical routing exists but is "
                "outside the configured TTL window."
            ),
        )

    def record_hit(self, *, destination_butler: str) -> None:
        """Record a successful affinity hit."""
        self.hit.add(
            1,
            {
                "source": "email",
                "destination_butler": (
                    str(destination_butler)[:64] if destination_butler else "unknown"
                ),
                "policy_tier": "affinity",
                "schema_version": "thread_affinity.v1",
            },
        )

    def record_miss(self, *, reason: str) -> None:
        """Record an affinity miss.

        reason must be one of: no_thread_id, no_history, conflict, disabled, error, stale.
        """
        safe_reason = reason if reason in _ALLOWED_AFFINITY_MISS_REASONS else "no_history"
        self.miss.add(
            1,
            {
                "source": "email",
                "reason": safe_reason,
                "schema_version": "thread_affinity.v1",
            },
        )

    def record_stale(self) -> None:
        """Record a stale affinity match (history exists but outside TTL)."""
        self.stale.add(
            1,
            {
                "source": "email",
                "schema_version": "thread_affinity.v1",
            },
        )


_THREAD_AFFINITY_TELEMETRY: ThreadAffinityTelemetry | None = None


def get_thread_affinity_telemetry() -> ThreadAffinityTelemetry:
    """Return the process-level thread affinity telemetry singleton."""
    global _THREAD_AFFINITY_TELEMETRY
    if _THREAD_AFFINITY_TELEMETRY is None:
        _THREAD_AFFINITY_TELEMETRY = ThreadAffinityTelemetry()
    return _THREAD_AFFINITY_TELEMETRY


def reset_thread_affinity_telemetry_for_tests() -> None:
    """Test helper to reset the thread affinity telemetry singleton."""
    global _THREAD_AFFINITY_TELEMETRY
    _THREAD_AFFINITY_TELEMETRY = None


__all__ = [
    "TriageTelemetry",
    "ThreadAffinityTelemetry",
    "get_triage_telemetry",
    "get_thread_affinity_telemetry",
    "reset_triage_telemetry_for_tests",
    "reset_thread_affinity_telemetry_for_tests",
]
