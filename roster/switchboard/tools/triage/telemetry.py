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


__all__ = [
    "TriageTelemetry",
    "get_triage_telemetry",
    "reset_triage_telemetry_for_tests",
]
